"""Play, and which loadout and which profile are live.

``core.play`` works out what Play does and does it (Stage 2.6); this is the
window's side of it -- refusing while the game runs, asking, saying what
happened, starting the game and watching for its loader's log -- and the
record of which loadout, and which of its profiles, is the one the game
loads: making it live, reading it back, and the profiles themselves (choose,
new, rename, delete).  Held as ``window.live`` (Stage 3.3 of
docs/proposals/consolidation-proposal.md).
"""

from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from ..core import build, disk, games, library, play, switches, truth
from ..core.changes import loadout_name
from ..core.i18n import tr, tr_kw, trn
from .widgets import busy_cursor, listing, named, tell

# How long after Play starts the game its loader's log may begin and still be
# Play's start: long enough for Steam, the launcher and a slow disk.
LAUNCH_WINDOW = 15 * 60


class LiveController:
    """The window's live, moved off it; ``window`` is the MainWindow."""

    def __init__(self, window) -> None:
        self.window = window
        # Watching for the game's loader to write its log after Play started
        # it: the timer, while it runs, and when to give up.
        self.log_timer = None
        self.log_deadline = 0.0

    def play(self, confirm: bool = True) -> bool:
        """Make this loadout live, then start the game -- or, where Play's
        menu says to play this loadout's mod in another loadout that has it,
        that one (:meth:`play_in`)."""
        target = self.play_target()
        if target is not None:
            return self.play_in(target)
        return self.make_live(confirm=confirm, launch=True)

    def sync_loadout(self, confirm: bool = True) -> bool:
        """Game ▸ Make this live…: :meth:`make_live` without starting
        the game, for somebody who starts it another way."""
        return self.make_live(confirm=confirm, launch=False)

    def make_live(self, confirm: bool = True, launch: bool = False, quiet: bool = False) -> bool:
        """Make the game load exactly this loadout, and with ``launch`` start it.

        One act, in order: refuse while the game runs; say what does not
        validate, without blocking; build the loadout's own mods and patches
        into the library; make the switches match -- a link for every library
        mod it has on and none for the rest, the game's own switch for
        anything else, everything it does not mention off; for Anno 117 write
        its profile, point the game at it and write the mods' options; for
        Anno 1800 write the iModYourAnno settings into the library's copies;
        then remember it as the live one, and start the game through its
        store.

        Every loadout is complete when played, not only a loadout: a mod of
        your own played with nothing else on is that mod in isolation, which
        is exactly what testing it wants.  Nothing is deleted and no mod of
        anybody's is rewritten; what moves is the switch the game reads, the
        Studio's own builds, and the Studio's own library copies.
        """

        if self.window.index is None:
            return False
        # A mod updated on disk since it was read is read again first, and
        # waited for below with the rest: what is checked and built here is
        # its new version (docs/proposals/projects-and-loadouts-proposal.md,
        # section 8).
        self.window.readings.follow()
        # Not ahead of what the window is still reading or checking -- but
        # the game's loader: its verdicts are waited for on their own, in
        # _plan_live, where the wait can be skipped (H2-15).
        if not self.window.background.wait(self.window, but=("loader",)):
            return False
        game = games.get(self.window.loadout.game)
        if launch and not self.window.has_something_to_play():
            # No loadout: nothing to apply, and an empty one made live would
            # switch every mod off -- which nobody pressing Play meant.
            if self.window.prompts.game_running(self.window.loadout.game, tr("It is running already.")):
                return False
            self.window.status_label.setText(
                tr("No loadout, so nothing to apply: your mods folder is as it was.")
            )
            return self._launch()
        if self.window.prompts.game_running(
            self.window.loadout.game,
            tr("Close the game first. It read its mods when it started, so "
               "switching them now does not change what it has loaded, and "
               "your mods folder would no longer match the running game."),
        ):
            return False
        folder = self.window.where.mods_folder()
        if not folder:
            QMessageBox.information(
                self.window,
                tr("No mods folder"),
                tr("The Studio does not know where %s's mods folder is. Load the "
                   "game's files from its installation on the Start tab, or read "
                   "a mods folder in, and try again.", game.label),
            )
            return False

        # A mod of yours given a new ModID is another mod, built into a
        # folder of its own: moved there before the plan is worked out, so
        # the plan builds and links it, and switches the old one off.
        self.window.library_controller.give_own_folders()
        plan = self._plan_live(folder)
        if plan.nothing_to_do:
            # Nothing to ask about -- but the profile is written all the same:
            # every Play leaves it saying exactly this loadout, so the loader's
            # log is checked against something certain.
            if plan.own and plan.pointer_folder is not None:
                written = play.write_own_profile(
                    self.window.loadout, plan.pointer_folder, plan.own_name, plan.found,
                    self._live_display_name(),
                )
                if written.failed:
                    QMessageBox.warning(
                        self.window, tr("The profile could not be written"),
                        "\n".join("%s: %s" % pair for pair in written.failed[:12]),
                    )
            # Nothing moved a folder: the plan's reading of them stands.
            self.note_live(inventory=plan.found.installed if plan.found else None)
            self.window.refresh_truth(say=False, inventory=plan.found.installed if plan.found else None)
            if launch:
                # Nothing asked, so said: Play did look.
                self.window.status_label.say(tr(
                    "Your mods folder already has the same mods switched "
                    "on as this loadout, and no others."
                ), folder)
                return self._launch()
            if quiet:
                return True
            QMessageBox.information(
                self.window,
                tr("Already live"),
                tr("Your mods folder already has the same mods switched on "
                   "as this loadout, and no others. The Studio's profile "
                   "matches, and is the live one.")
                if plan.own
                else tr("Your mods folder already has the same mods switched "
                        "on as this loadout, and no others."),
            )
            return True
        if confirm and not self.window.prompts.confirm_live(plan, launch, self.window.loadout, self.window.files.loadout_name()):
            return False

        written = play.carry_out(
            plan, self.window.loadout, self.window.where.library_dir(), backup_into=self.window.output.backup_into,
            display_name=self._live_display_name(),
        )
        self.window.output._show_build()
        if plan.numbered:
            # Play records no checkpoint and saves nothing, so the new number
            # is an edit like any other: kept when the loadout is.
            self.window.files.on_loadout_edited()
        if written.failed:
            QMessageBox.warning(
                self.window,
                tr("Some of it could not be done"),
                "\n".join("%s: %s" % pair for pair in written.failed[:12]),
            )
        # The folders moved: read once more, and that reading serves both.
        now = self.note_live()
        self.window.refresh_truth(say=False, inventory=now)
        self.window.start.refresh_source_panel()
        said = written.describe()
        if plan.built:
            # Translators: %(status)s says what making it live did.
            said = tr("Built %(mods)s into the library. %(status)s",
                      mods=listing(plan.built_names), status=said)
        if plan.options:
            said = trn("%(status)s  Wrote options for %(n)s mod into %(file)s.",
                       "%(status)s  Wrote options for %(n)s mods into %(file)s.",
                       len(plan.options), status=said, file=switches.OPTIONS_FILE)
        if plan.tweaks:
            said = trn("%(status)s  Wrote iModYourAnno settings for %(n)s mod into a "
                       "generated copy.",
                       "%(status)s  Wrote iModYourAnno settings for %(n)s mods into "
                       "generated copies.",
                       len(plan.tweaks), status=said)
        if plan.edited:
            said = trn("%(status)s  Wrote your edits to %(n)s mod into a generated copy.",
                       "%(status)s  Wrote your edits to %(n)s mods into generated copies.",
                       len(plan.edited), status=said)
        self.window.status_label.say(said, folder)
        if launch:
            return self._launch()
        return True

    def played_elsewhere(self) -> list:
        """The other loadouts that have the mod this loadout is
        (:meth:`.Loadout.stands_for`), for Play's menu: as ``(file, name,
        whether the game loads it)``, the one it loads first, then by name.
        None for a loadout made to play."""
        origin = self.window.loadout.stands_for()
        output = self.window.output
        if origin is None or origin not in self.window.loadout.projects:
            return []
        having = [(Path(path), loadout_name(path), output.is_live(path))
                  for path in output.loadouts_having(origin)]
        return sorted(having, key=lambda one: (not one[2], one[1].lower()))

    def _choice_key(self) -> str:
        """Where this loadout's choice on Play's menu is kept: by its file,
        "" before it has one."""
        path = self.window.loadout_path
        return self._key_for(path) if path is not None else ""

    @staticmethod
    def _key_for(path) -> str:
        import hashlib

        return "playIn/" + hashlib.sha1(str(Path(path)).lower().encode("utf-8")).hexdigest()[:16]

    def follow_rename(self, was, now) -> None:
        """A loadout's file moved from ``was`` to ``now``: its choice on
        Play's menu goes with it, and so does every other loadout's choice to
        play in it.  Kept by its file, the choice was lost to a rename
        (R3-15, hands-on run 3)."""
        settings = self.window.settings
        old, new = self._key_for(was), self._key_for(now)
        if old != new:
            chosen = settings.value(old)
            if chosen is not None:
                settings.setValue(new, chosen)
                settings.remove(old)
        spelt = os.path.normcase(os.path.abspath(str(was)))
        for key in settings.allKeys():
            if not key.startswith("playIn/"):
                continue
            chosen = str(settings.value(key, "") or "")
            if chosen and os.path.normcase(os.path.abspath(chosen)) == spelt:
                settings.setValue(key, str(now))

    def choose_play(self, target) -> None:
        """Play's menu: play this loadout's mod in the loadout at ``target``
        from now on, or here, on its own, for None."""
        key = self._choice_key()
        if key:
            self.window.settings.setValue(key, str(target) if target is not None else "")

    def play_target(self):
        """The loadout Play's menu says to play this loadout's mod in, by
        its file, or None to play this one: none picked, or the one picked
        no longer has the mod."""
        key = self._choice_key()
        chosen = str(self.window.settings.value(key, "") or "") if key else ""
        if not chosen:
            return None
        for path, _name, _live in self.played_elsewhere():
            if disk.same_file(path, chosen):
                return path
        return None

    def play_in(self, path: Path) -> bool:
        """Play in Main: the mod this loadout is built again for the
        loadouts that have it (*Update*, which saves this loadout first),
        then the loadout at ``path`` opened and played -- where somebody
        going to play it wants to be, and its own Play builds its own edits
        (docs/proposals/export-tab-proposal.md, section 5.6)."""
        origin = self.window.loadout.stands_for()
        if origin is None or not self.window.output.update_build(origin):
            return False
        if not self.window.load_loadout(path):
            return False
        return self.play()

    def _plan_live(self, folder: str) -> play.Plan:
        """Everything making this loadout live would do: see :func:`core.play.plan`.

        The game's loader is asked first, outside the busy cursor: it may
        wait for the loader under a dialog of its own."""
        verdicts = self.window.loader_check.loader_verdicts(build.mine(self.window.loadout))
        with busy_cursor():
            return play.plan(
                self.window.loadout, folder, self.window.where.library_dir(), index=self.window.index,
                verdicts=lambda _built: verdicts, repeats=self.repeats_playing(),
            )

    def _launch(self) -> bool:
        """Start the game: the way chosen in the settings, else through its
        store; say so, or say why not and offer to choose."""
        from ..core import launch as launch_mod

        game = games.get(self.window.loadout.game)
        address = self.window.preferences.game_start() or launch_mod.uri(
            launch_mod.install_for(self.window.loadout.game, self.window.game_folder)
        )
        # A choice is kept once it has started the game, and one that does
        # not is offered again by name: kept untried, Play said nothing.
        chosen = ""
        while not address or not launch_mod.start(address):
            address = chosen = self.window.prompts.ask_game_start(game.label, address)
            if not chosen:
                return False
        if chosen:
            self.window.preferences.set_game_start(self.window.loadout.game, chosen)
        # Translators: %(status)s is what the status bar said already.
        self.window.status_label.say(tr(
            "%(status)s  Starting %(game)s. What its loader logs as loaded "
            "is checked once the mods are in.",
            status=self.window.status_label.text(), game=game.label,
        ), self.window.status_label.folder)
        with contextlib.suppress(OSError):
            library.note_launched(self.window.where.library_dir())
        self._watch_the_log()
        return True

    def restore_backup(self) -> None:
        """Put mods back the way they were before an export wrote over them."""
        from .backup_dialog import BackupDialog

        folder = self.window.where.mods_folder() or str(self.window.output.export_dir())
        dialog = BackupDialog(folder, self.window)
        dialog.exec()
        if dialog.restored:
            self.window.status_label.say(trn(
                "Put %(n)s file back in %(folder)s.  Read the folder again to "
                "bring that into this loadout.",
                "Put %(n)s files back in %(folder)s.  Read the folder again to "
                "bring that into this loadout.",
                dialog.restored, folder=folder,
            ), folder)
        dialog.deleteLater()

    def repeats_playing(self, found=None) -> list:
        """The GUIDs the loadout's loading mods take twice, one sentence
        each: for the card and for Play, where which asset the game finds is
        about to matter. ``found`` is :func:`core.repeats.find`'s, when the
        caller has it already."""
        from ..core import repeats

        def name(origin: str) -> str:
            return self.window.loadout.label_of(origin) or tr("your mod")

        if found is None:
            found = repeats.find(self.window.loadout, self.window.index)
        return repeats.before_play(found, name)

    def _watch_the_log(self) -> None:
        """After Play: look for the loader's log of the game just started,
        and say what it found once the game has loaded its mods.  Not a
        dialog -- somebody has just started a game."""
        from PySide6.QtCore import QTimer

        timer = self.log_timer
        if timer is None:
            timer = self.log_timer = QTimer(self.window)
            timer.setInterval(3000)
            timer.timeout.connect(self.look_for_the_log)
        self.log_deadline = time.time() + 30 * 60
        timer.start()

    def look_for_the_log(self) -> bool:
        """One look; whether the log was found."""
        from ..core import loaderlog

        if time.time() > self.log_deadline:
            self.log_timer.stop()
            return False
        # The log alone until it is the new one and finished: the folders are
        # asked again only then, once, rather than every few seconds of a game.
        log = loaderlog.read(self.window.loadout.game)
        if log is None or not log.complete or log.started < library.live_since(self.window.where.library_dir()) - 1:
            return False
        self.window.refresh_truth(say=False)
        found = self.window.truth
        if found is None or found.log_check is None:
            return False
        self.log_timer.stop()
        self.window.status_label.setText(found.log_check.describe())
        return True

    def check_the_log(self, found) -> None:
        """What the game's loader said it loaded, beside the live loadout --
        when the game has started, and finished loading, since it was made
        live."""
        from ..core import loaderlog

        log = loaderlog.read(self.window.loadout.game)
        held = library.live_record(self.window.where.library_dir())
        since = library.live_since(self.window.where.library_dir())
        if log is None or not log.complete or log.started < since - 1:
            return
        found.started_since = True
        checked = loaderlog.check(log, found, self.window.loadout)
        # Where it came from: a game that started within a few minutes of
        # Play starting one is Play's; any other is one started some other
        # way -- from Steam the next day, most often -- and counts the same,
        # so long as the folder is still as Play left it.
        launched = library.launched_at(self.window.where.library_dir())
        checked.by_play = bool(launched) and launched - 5 <= log.started <= launched + LAUNCH_WINDOW
        stamp = held.get("fingerprint") or ""
        checked.changed = bool(stamp) and stamp not in (
            truth.fingerprint(found), truth.fingerprint_before(found))
        found.log_check = checked

    def live_loadout(self) -> str:
        """The file of the loadout the Studio last made live, or "".

        One whose file is gone is nothing: a loadout deleted or moved outside
        the Studio cannot be what anybody is playing by name.  The open
        loadout counts before its first save, since it is right here.
        """
        held = library.live(self.window.where.library_dir())
        if not held:
            return ""
        if Path(held).is_file() or disk.same_file(held, self.window.loadout_path):
            return held
        return ""

    def note_live(self, inventory=None):
        """This loadout is what the game loads now.  Any loadout: every one
        is complete when played.  Remembered with what the mods folder says
        now, so the loader's log can later be told apart from a game started
        after something else changed it.

        ``inventory`` is the folders as just read, when nothing has moved
        since; without it they are read here.  Returns the inventory the
        record was taken from, for whatever asks next, or None.
        """
        if self.window.loadout_path is None:
            return None
        # A pointer at a file that was never written points at nothing.
        self.window.files.ensure_loadout_file()
        folder = self.window.where.mods_folder()
        stamp = ""
        if folder:
            try:
                found = truth.read(
                    self.window.loadout, folder, in_folders=inventory,
                    library_dir=self.window.where.library_dir(),
                )
                stamp = truth.fingerprint(found)
                inventory = found.installed
            except OSError:
                stamp = ""
                inventory = None
        # A label, not worth an error over the switch it follows.
        with contextlib.suppress(OSError):
            # A loadout of one profile is simply the loadout: the record names
            # none, which means its first, and the card and recents say the
            # loadout's name alone rather than "Main" after it.
            library.set_live(
                self.window.where.library_dir(), self.window.loadout_path, fingerprint=stamp,
                profile=self.saved_profile_name(self.window.loadout.chosen_profile().name)
                if len(self.window.loadout.profiles) > 1 else "",
            )
        return inventory

    def wrote_loadout(self, path) -> None:
        """The loadout was just written to its own file, ``path``: its one
        profile, if it had no name, has the default one, and the live record
        follows any profile renamed since the file was last written."""
        self.window.loadout.name_first_profile()
        # The record's name looked up once: renames followed one by one would
        # move it twice when two profiles swap names, back onto the other one.
        before = library.live_profile(self.window.where.library_dir())
        after = self.window.profile_renames.get(before, before)
        if before and after != before:
            with contextlib.suppress(OSError):
                library.follow_live_profile(self.window.where.library_dir(), path, before, after)
        self.window.profile_renames.clear()

    def saved_profile_name(self, name: str) -> str:
        """What profile ``name`` is called in the loadout's file: its name
        before any rename not yet saved."""
        for before, after in self.window.profile_renames.items():
            if after == name:
                return before
        return name

    def live_profile_here(self):
        """Which of this loadout's profiles the game loads, by its name here,
        or None when none of them is: another loadout is live, or the record
        names a profile this one no longer has. A record from before profiles
        names none, and means the first -- its loadout had one."""
        live = self.live_loadout()
        if not live or not disk.same_file(live, self.window.loadout_path):
            return None
        held = library.live_profile(self.window.where.library_dir())
        names = self.window.loadout.profile_names()
        if not held:
            return names[0]
        held = self.window.profile_renames.get(held, held)
        return held if held in names else None

    def live_name(self, live) -> str:
        """The live loadout and profile, as the card's header names them:
        *Anno 1800 loadout — Profile1*, or the loadout alone when its
        profile is named after it, as a loadout of one profile is."""
        name = self.window.files.name_of(live)
        held = library.live_profile(self.window.where.library_dir())
        if disk.same_file(live, self.window.loadout_path):
            held = self.live_profile_here() or held
        return "%s — %s" % (name, held) if held and held != name else name

    def live_profile_shown(self, game: str) -> str:
        """The live loadout's profile, for its line on the recents list, or ""
        when naming it says nothing."""
        live = self.live_for(game)
        if live is None:
            return ""
        held = library.live_profile(self.window.where.library_dir(game))
        return held if held and held != self.window.files.name_of(live) else ""

    def _live_display_name(self) -> str:
        """What 117's own mod menu calls the profile Play writes: the loadout,
        and which of its profiles when it has more than one."""
        name = self.window.files.loadout_name() or self.window.loadout.own.name
        if len(self.window.loadout.profiles) > 1:
            return "%s — %s" % (name, self.window.loadout.chosen_profile().name)
        return name

    def choose_profile(self, name: str) -> bool:
        """Work in profile ``name``: its switches are the layers' from now on.
        Says what Play would change, before anything is written."""
        try:
            moved = self.window.loadout.choose_profile(name)
        except KeyError:
            return False
        if not moved:
            return False
        self._profiles_moved()
        found = self.window.truth
        if found is None:
            said = tr("Working in %s.", name)
        elif found.is_live and found.matches:
            said = tr("Working in %s. It is what the game loads.", name)
        else:
            said = tr("Working in %(profile)s. %(state)s",
                      profile=name, state=found.describe())
        self.window.status_label.setText(said)
        return True

    def new_profile(self, name: str = "") -> bool:
        """A new profile of this loadout, switched as the chosen one is, and
        chosen."""
        if not name:
            loadout, shown = self.window.files.loadout_name(), self.profile_shown()
            if loadout:
                prompt = tr(
                    "A new profile of %(loadout)s: the same mods, switched as "
                    "%(profile)s is now.\n"
                    "Switch mods on and off in it without touching the "
                    "others.\n\nCall it:", loadout=loadout, profile=shown,
                )
            elif shown:
                prompt = tr(
                    "A new profile of this loadout: the same mods, switched as "
                    "%s is now.\n"
                    "Switch mods on and off in it without touching the "
                    "others.\n\nCall it:", shown,
                )
            else:
                prompt = tr(
                    "A new profile of this loadout: the same mods, switched as "
                    "this loadout is now.\n"
                    "Switch mods on and off in it without touching the "
                    "others.\n\nCall it:"
                )
            name = self.window.prompts.ask_profile_name(
                tr("New profile"), prompt, self._free_profile_name(),
            )
        if not name:
            return False
        was = self.profile_shown()
        # With two, each needs a name.
        self.window.loadout.name_first_profile()
        try:
            made = self.window.loadout.add_profile(name)
        except ValueError as exc:
            QMessageBox.warning(self.window, tr("New profile"), str(exc))
            return False
        self.window.loadout.choose_profile(made.name)
        self._profiles_moved()
        if was:
            said = tr(
                "Made %(made)s, switched as %(was)s was. Mods switched here "
                "are switched in it only.", made=made.name, was=was,
            )
        else:
            said = tr(
                "Made %s, switched as this loadout was. Mods switched here are "
                "switched in it only.", made.name,
            )
        self.window.status_label.setText(said)
        return True

    def rename_profile(self, name: str = "") -> bool:
        """Call the chosen profile something else."""
        chosen = self.window.loadout.chosen_profile()
        old = chosen.name
        was = old or self.window.files.loadout_name()
        if not name:
            name = self.window.prompts.ask_profile_name(tr("Rename profile"), tr("Call %s:", was), was)
        if not name or name.strip() == chosen.name:
            return False
        try:
            self.window.loadout.rename_profile(chosen.name, name)
        except ValueError as exc:
            QMessageBox.warning(self.window, tr("Rename profile"), str(exc))
            return False
        self.window.profile_renames[self.saved_profile_name(old)] = chosen.name
        self._profiles_moved()
        self.window.status_label.setText(
            tr("Renamed %(old)s to %(new)s.", old=was, new=chosen.name))
        return True

    def delete_profile(self) -> bool:
        """Delete the chosen profile, and choose the first of the rest.  Not
        the last, and not the one the game loads."""
        chosen = self.window.loadout.chosen_profile()
        if len(self.window.loadout.profiles) < 2:
            return False
        if self.live_profile_here() == chosen.name:
            tell(
                self.window, tr("Delete profile"),
                tr_kw("%s is what the game loads. Make another profile [[live]] "
                      "first, then delete this one.", named(chosen.name)),
            )
            return False
        if not self.window.prompts.ask_delete_profile(chosen.name):
            return False
        gone = chosen.name
        self.window.loadout.delete_profile(gone)
        self._profiles_moved()
        self.window.status_label.setText(tr(
            "Deleted %(gone)s. Now working in %(now)s. Its mods are still in "
            "the loadout, and in every profile that has them.",
            gone=gone, now=self.window.loadout.chosen_profile().name,
        ))
        return True

    def _profiles_moved(self) -> None:
        """The chosen profile, or the list of them, has changed: every view
        of the switches follows. Counted as unsaved, as a switch is."""
        self.window.files.mark_unsaved()
        self.window.changes_panel.refresh()
        self.window.editor.refresh_from_loadout()
        self.window.refresh_truth(say=False, disk=False)
        self.window.mark_output_dirty()
        self.window.record()
        self.window.start.refresh_source_panel()
        self.window.update_title()

    def profile_shown(self) -> str:
        """The chosen profile's name, else the loadout's; "" when neither has
        one, which each sentence says as "this loadout" in its own words."""
        return self.window.loadout.chosen_profile().name or self.window.files.loadout_name()

    def _free_profile_name(self) -> str:
        # A name written into the loadout file, so the same on every PC.
        taken = {name.casefold() for name in self.window.loadout.profile_names()}
        number = len(taken) + 1
        while ("profile %d" % number) in taken:
            number += 1
        return "Profile %d" % number

    def live_for(self, game: str):
        """The live loadout's file for ``game`` (the open one's when not
        given), if it is still there."""
        game = game or self.window.loadout.game
        if not game:
            return None
        held = library.live(self.window.where.library_dir(game))
        return Path(held) if held and Path(held).is_file() else None
